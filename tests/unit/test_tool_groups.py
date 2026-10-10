"""Tool groups: the group of each tool, what the prompt says about the groups, the groups a thread used, how a run offers
its tools, the groups a step turns on and the tools a run can call."""

from pathlib import Path

import pytest

from dotagent.agent.compact import estimate_tokens
from dotagent.agent.executor import ToolOutcome
from dotagent.agent.loop import _run_tools, _turned_on
from dotagent.agent.prompt import PromptInputs, build_system_prompt, collect_prompt_inputs
from dotagent.agent.scaffold import Scaffold, surface_for
from dotagent.config import AgentConfig, AppConfig, Tier, load_default_config
from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolCall, ToolSpec, assistant_message, tool_message, user_message
from dotagent.runtime import _tool_groups
from dotagent.tools.base import ToolResult, fail, ok
from dotagent.tools.campaign import CAMPAIGN_TOOLS
from dotagent.tools.groups import (
    CORE_GROUP,
    group_lines,
    group_of,
    groups_event,
    groups_of,
    hidden_tools,
    offered_specs,
    used_groups,
)
from dotagent.tools.posts import POST_TOOLS
from dotagent.tools.registry import ToolRegistry, build_registry, specs_for_mode

_DEFAULT: AppConfig = load_default_config()
# Every built-in tool: the desktop ones exist only when the desktop is enabled.
REGISTRY: ToolRegistry = build_registry(
    _tool_groups(_DEFAULT.model_copy(update={"desktop": _DEFAULT.desktop.model_copy(update={"enabled": True})}), True)
)
# What a task run is offered when it is not a session of a campaign: the loop leaves the campaign tools out.
TASK_SPECS: tuple[ToolSpec, ...] = tuple(
    spec for spec in specs_for_mode(REGISTRY, "task") if spec.name not in CAMPAIGN_TOOLS | POST_TOOLS
)
TASK_TOOLS: list[str] = [spec.name for spec in TASK_SPECS]
DEFAULTS: AgentConfig = _DEFAULT.agent
GROUPED: AgentConfig = DEFAULTS.model_copy(update={"tool_surface": "grouped"})
AUTO: AgentConfig = DEFAULTS.model_copy(update={"tool_surface": "auto"})


@pytest.mark.parametrize(
    ("tool", "group"),
    [
        ("read_file", "core"),
        ("shell", "core"),
        ("web_fetch", "core"),
        ("memory_save", "core"),
        ("memory_search", "core"),
        ("delegate", "core"),
        ("consult", "core"),
        ("use_tools", "core"),
        ("browser_open", "browser"),
        ("desktop_act", "desktop"),
        ("apple_script", "desktop"),
        ("schedule_list", "schedule"),
        ("terminal_send", "terminal"),
        ("memory_update", "memory_admin"),
        ("memory_forget", "memory_admin"),
        ("mcp__github__create_issue", "mcp__github"),
        ("mcp__team-wiki__search__all", "mcp__team-wiki"),
    ],
)
def test_each_tool_is_in_the_group_of_what_it_reaches(tool: str, group: str) -> None:
    assert group_of(tool) == group


def test_the_core_group_holds_what_most_runs_need_and_is_about_a_third_of_the_tool_definitions() -> None:
    assert groups_of(TASK_TOOLS) == ("browser", "memory_admin", "schedule", "terminal", "desktop")
    assert groups_of([spec.name for spec in specs_for_mode(REGISTRY, "research")]) == ("browser", "schedule", "desktop")
    core: tuple[ToolSpec, ...] = offered_specs(TASK_SPECS, frozenset({CORE_GROUP}))
    assert {spec.name for spec in core} == {
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "search_files",
        "find_files",
        "shell",
        "web_search",
        "web_fetch",
        "web_crawl",
        "web_extract",
        "memory_save",
        "memory_search",
        "write_notes",
        "delegate",
        "consult",
        "finish",
        "ask_user",
        "notify",
        "update_plan",
        "mark_plan_step",
        "use_tools",
        "skill",
        "judge_items",
    }
    # web_crawl and web_extract (item 41) and write_notes (item 34) joined the core: measured share 0.412 before the
    # notes tool, with the descriptions of the crawler's tools trimmed.
    assert estimate_tokens([], core) < 0.45 * estimate_tokens([], TASK_SPECS)


def test_the_tools_of_the_groups_that_are_off_are_hidden_with_their_group() -> None:
    on: frozenset[str] = frozenset({CORE_GROUP, "schedule"})
    offered: tuple[ToolSpec, ...] = offered_specs(TASK_SPECS, on)
    hidden: dict[str, str] = hidden_tools(TASK_SPECS, on)
    assert {spec.name for spec in offered} | set(hidden) == set(TASK_TOOLS)
    assert {spec.name for spec in offered} & set(hidden) == set()
    assert {"schedule_create", "schedule_list", "schedule_delete"} <= {spec.name for spec in offered}
    assert set(hidden.values()) == {"browser", "desktop", "memory_admin", "terminal"}
    assert (hidden["apple_script"], hidden["memory_forget"]) == ("desktop", "memory_admin")


def _inputs(tool_names: list[str]) -> PromptInputs:
    return collect_prompt_inputs(
        "task", "cli", "aggressive", "ask", "wait", False, Path("/tmp/work"), [], tool_names, [], None, []
    )


def test_the_prompt_lists_the_groups_only_when_the_tools_are_grouped() -> None:
    inputs: PromptInputs = _inputs(TASK_TOOLS)
    grouped: str = build_system_prompt(inputs)
    full: str = build_system_prompt(
        inputs.model_copy(update={"tool_names": tuple(name for name in TASK_TOOLS if name != "use_tools")})
    )
    assert "# Tool groups" not in full
    start: int = grouped.index("# Tool groups\n")
    end: int = grouped.index("\n\n", start)
    # The section is all that grouping adds to the prompt.
    assert grouped[:start] + grouped[end + 2 :] == full
    section: list[str] = grouped[start:end].split("\n")
    assert "Turn a group on with use_tools before you use its tools." in section[1]
    assert section[2:] == [
        "- browser: Chrome, for pages that need JavaScript, a log-in or clicking (browser_open, browser_new_tab, "
        "browser_snapshot, browser_find, browser_links, browser_act, browser_scroll, browser_read_text, "
        "browser_screenshot, browser_wait_text, browser_back, browser_switch_tab, browser_close_tab, browser_upload)",
        "- memory_admin: correcting or forgetting saved notes (memory_update, memory_forget)",
        "- schedule: goals that run later, once or on a cron expression (schedule_create, schedule_list, "
        "schedule_delete)",
        "- terminal: programs that ask questions, REPLs and servers that stay up, on a terminal of their own "
        "(terminal_open, terminal_send, terminal_read, terminal_close)",
        "- desktop: the Mac's applications, windows, clipboard and AppleScript (desktop_apps, desktop_open_app, "
        "desktop_snapshot, desktop_screenshot, desktop_act, desktop_read, desktop_clipboard_read, "
        "desktop_clipboard_write, apple_script)",
    ]


def test_a_large_mcp_server_is_one_group_whose_line_names_some_of_its_tools() -> None:
    tools: list[str] = ["read_file", *(f"mcp__wiki__page_{index:02d}" for index in range(20))]
    shown: str = ", ".join(f"mcp__wiki__page_{index:02d}" for index in range(16))
    assert group_lines(tools) == (f"- mcp__wiki: the tools of the MCP server wiki ({shown} and 4 more)",)


def _call(call_id: str, name: str, arguments: JsonObject) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def test_a_thread_brings_back_the_groups_whose_tools_its_model_called() -> None:
    opened: ToolCall = _call("1", "browser_open", {"url": "https://example.org"})
    history = [
        user_message("Book a table for two", ()),
        assistant_message("", None, (opened, _call("2", "read_file", {"path": "notes.md"}))),
        tool_message(opened, "Opened.", ()),
        # Turning a group on without calling its tools brings nothing back.
        assistant_message("", None, (_call("3", "use_tools", {"group": "schedule"}),)),
        assistant_message("", None, (_call("4", "mcp__wiki__search", {"query": "table"}),)),
    ]
    assert used_groups(history) == frozenset({"browser", "core", "mcp__wiki"})


def _scaffold(tier: Tier, context_tokens: int) -> Scaffold:
    return Scaffold(
        model="ollama:a",
        window=None,
        context_tokens=context_tokens,
        tool_output_chars=12_000,
        tier=tier,
        reason="",
        reminder_every=None,
    )


def test_auto_groups_the_tools_of_a_weak_model_and_of_a_window_that_cut_the_budget() -> None:
    assert DEFAULTS.tool_surface == "full"
    assert surface_for(DEFAULTS, _scaffold("weak", 18_000)) == ("full", '[agent] tool_surface is "full"')
    assert surface_for(GROUPED, _scaffold("standard", 48_000)) == ("grouped", '[agent] tool_surface is "grouped"')
    assert surface_for(AUTO, _scaffold("weak", 48_000)) == (
        "grouped",
        '[agent] tool_surface is "auto" and the model is in the weak tier',
    )
    assert surface_for(AUTO, _scaffold("standard", 18_000)) == (
        "grouped",
        '[agent] tool_surface is "auto" and the model\'s window cut the context budget to 18,000 tokens',
    )
    assert surface_for(AUTO, _scaffold("standard", 48_000))[0] == "full"


def _outcome(call_id: str, name: str, arguments: JsonObject, result: ToolResult) -> ToolOutcome:
    return ToolOutcome(call=_call(call_id, name, arguments), result=result, seconds=0.0, action=None)


def test_a_step_turns_on_the_groups_of_the_off_tools_it_called_and_of_use_tools_that_worked() -> None:
    hidden: dict[str, str] = {"browser_open": "browser", "schedule_list": "schedule"}
    outcomes: list[ToolOutcome] = [
        _outcome("1", "browser_open", {"url": "https://example.org"}, fail("did not run")),
        _outcome("2", "use_tools", {"group": " Desktop "}, ok("on")),
        _outcome("3", "use_tools", {"group": "calendar"}, fail("no such group")),
        _outcome("4", "use_tools", {"group": "core"}, ok("always on")),
        _outcome("5", "read_file", {"path": "notes.md"}, ok("text")),
        _outcome("6", "browser_open", {"url": "https://example.com"}, fail("did not run")),
    ]
    assert _turned_on(outcomes, hidden, frozenset({CORE_GROUP})) == ("browser", "desktop")
    assert _turned_on(outcomes, {}, frozenset({CORE_GROUP, "desktop"})) == ()
    assert groups_event(3, ("core", "browser", "desktop"), frozenset({"core", "desktop"}), ("desktop",), "") == {
        "step": 3,
        "on": ["core", "desktop"],
        "off": ["browser"],
        "turned_on": ["desktop"],
        "reason": "",
    }


def test_a_run_can_call_the_tools_it_offers_and_the_tools_of_other_modes_only() -> None:
    offered: tuple[ToolSpec, ...] = tuple(
        spec for spec in specs_for_mode(REGISTRY, "chat") if spec.name not in {"use_tools", "delegate"}
    )
    tools: ToolRegistry = _run_tools(REGISTRY, "chat", offered)
    assert {"use_tools", "delegate"} & set(tools.tools) == set()
    assert {"read_file", "browser_open", "memory_forget"} <= set(tools.tools)
    # finish is not a chat tool: a call of it is answered as not available in chat mode, not as unknown.
    assert "finish" in tools.tools
