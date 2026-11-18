"""A run whose model has a small window leaves tools out instead of not running (item 1211): the owner's own servers'
tools go first, the largest first, and finish, use_tools and ask_user always stay. No model service is used.
"""

from dotagent.agent.compact import estimate_tokens
from dotagent.agent.loop import _fit_tools
from dotagent.agent.scaffold import Scaffold
from dotagent.llm.types import ToolSpec, system_message
from dotagent.tools.meta import ASK_USER_TOOL, FINISH_TOOL, USE_TOOLS_TOOL


def _scaffold(context_tokens: int) -> Scaffold:
    return Scaffold(
        model="ollama:small",
        window=context_tokens,
        chosen=context_tokens,
        context_tokens=context_tokens,
        tool_output_chars=4000,
        tier="standard",
        reason="the test's",
        reminder_every=None,
    )


def _spec(name: str, size: int) -> ToolSpec:
    return ToolSpec(name=name, description="x" * size, parameters={"type": "object", "properties": {}})


def test_a_small_window_keeps_the_core_tools_and_leaves_the_largest_owner_tools_out() -> None:
    system = system_message("s" * 400)
    core: tuple[ToolSpec, ...] = tuple(_spec(f"core_{index}", 40) for index in range(10))
    owner: tuple[ToolSpec, ...] = tuple(_spec(f"mcp__server__tool_{index}", 3000) for index in range(30))
    specs: tuple[ToolSpec, ...] = core + owner
    kept, left_out = _fit_tools(system, specs, _scaffold(9000))
    assert {spec.name for spec in core} <= {spec.name for spec in kept}
    assert left_out and all(name.startswith("mcp__") for name in left_out)
    assert estimate_tokens([system], kept) <= 9000


def test_a_window_with_room_leaves_nothing_out() -> None:
    system = system_message("s" * 400)
    specs: tuple[ToolSpec, ...] = tuple(_spec(f"core_{index}", 40) for index in range(10))
    kept, left_out = _fit_tools(system, specs, _scaffold(1_000_000))
    assert (kept, left_out) == (specs, ())


def test_the_tools_a_run_cannot_work_without_stay_even_when_they_alone_overfill_the_window() -> None:
    system = system_message("s" * 4000)
    must: tuple[ToolSpec, ...] = tuple(_spec(name, 3000) for name in (FINISH_TOOL, USE_TOOLS_TOOL, ASK_USER_TOOL))
    specs: tuple[ToolSpec, ...] = must + tuple(_spec(f"mcp__server__tool_{index}", 3000) for index in range(30))
    kept, left_out = _fit_tools(system, specs, _scaffold(100))
    assert {spec.name for spec in kept} == {FINISH_TOOL, USE_TOOLS_TOOL, ASK_USER_TOOL}, kept
    assert len(left_out) == 30
