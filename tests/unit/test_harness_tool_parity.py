"""The tools of the three ways a run works (item 1720): Dot's own loop, Claude Code on the owner's plan and OpenCode's
agent on Zen's free models. A tool the loop gives that a program's agent does not get is one of the lists below, each
with why; a tool added to Dot reaches all three unless one of them says otherwise here."""

from pathlib import Path

import pytest

from dotagent.agent.handover import door_tool
from dotagent.agent.loop import NOT_FOR_DELEGATED_RUNS
from dotagent.config import AppConfig, load_default_config
from dotagent.opencode.run import bridged
from dotagent.runtime import _tool_groups
from dotagent.store.models import RunMode
from dotagent.tools.registry import ToolRegistry, build_registry, tools_for_mode

# The loop's own and a campaign's, which neither program's agent gets; the plan's tools are both programs' since items
# 1693 and 1695.
_LOOP_ONLY: dict[str, str] = {
    "use_tools": "the loop's tool groups; Claude Code looks tools up with its own search, OpenCode lists them all",
    "react": "a reaction is on a message of the owner's in Dot's own chat",
    "campaign_achieved": "a campaign is judged by the facts and plan only Dot's own loop keeps",
    "campaign_note": "a campaign's",
    "campaign_schedule": "a campaign's",
    "campaign_targets": "a campaign's",
    "check_draft": "a campaign's post",
    "pick_draft": "a campaign's post",
    "rank_posts": "a campaign's post",
}
# Besides those, what OpenCode's agent does with its own tools; finish is its since item 1695.
_OPENCODE_ALSO_WITHOUT: dict[str, str] = {
    "read_file": "OpenCode's read",
    "write_file": "OpenCode's write",
    "edit_file": "OpenCode's edit",
    "list_dir": "OpenCode's list",
    "find_files": "OpenCode's glob",
    "search_files": "OpenCode's grep",
    "shell": "OpenCode's bash",
    "web_fetch": "OpenCode's webfetch",
    "web_search": "OpenCode's websearch",
}


@pytest.fixture
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ToolRegistry:
    """Every tool Dot may have but the Mac's desktop ones (macOS's frameworks), with each switch on."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    defaults: AppConfig = load_default_config()
    config: AppConfig = defaults.model_copy(
        update={
            "code": defaults.code.model_copy(update={"enabled": True}),
            "shell": defaults.shell.model_copy(update={"terminal_sessions": 2}),
            "phone": defaults.phone.model_copy(update={"enabled": True}),
            "policy": defaults.policy.model_copy(update={"code_sources": ["Xcode"]}),
        }
    )
    return build_registry(_tool_groups(config, True))


def _loop(registry: ToolRegistry, mode: RunMode, delegated: bool) -> set[str]:
    return {
        tool.spec.name
        for tool in tools_for_mode(registry, mode)
        if not (delegated and tool.spec.name in NOT_FOR_DELEGATED_RUNS)
    }


@pytest.mark.parametrize("mode", ["task", "chat", "research"])
@pytest.mark.parametrize("delegated", [False, True])
def test_a_program_lacks_only_the_tools_said_why(registry: ToolRegistry, mode: RunMode, delegated: bool) -> None:
    loop: set[str] = _loop(registry, mode, delegated)
    claude_code: set[str] = {name for name in loop if door_tool(name, delegated)}
    opencode: set[str] = {name for name in loop if bridged(name, delegated)}
    assert loop - claude_code == loop & set(_LOOP_ONLY)
    assert loop - opencode == loop & (set(_LOOP_ONLY) | set(_OPENCODE_ALSO_WITHOUT))


def test_opencode_gets_the_tools_it_lacked(registry: ToolRegistry) -> None:
    """What the audit of 2026-11-24 found missing on OpenCode (look, video, draft, wait, skill_save), the team's too."""
    wanted: set[str] = {
        "theme_read",
        "theme_apply",
        "theme_undo",
        "watch_video",
        "draft_message",
        "wait_until",
        "skill_save",
        "delegate",
        "message_agent",
        "show_page",
    }
    assert wanted <= {name for name in registry.tools if bridged(name, False)}
