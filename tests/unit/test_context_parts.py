"""A request's parts (item 735): the system prompt by section, the tools by group, the conversation by role; and the
lines `dotx stats` makes of the newest runs' first steps."""

from dotagent.agent.context_parts import OPENING, ContextParts, parts_of
from dotagent.cli_stats import render_context
from dotagent.llm.types import ChatRequest, ToolSpec, system_message, user_message


def _request(system: str, tools: tuple[str, ...]) -> ChatRequest:
    return ChatRequest(
        model="ollama:x",
        messages=(system_message(system), user_message("Do it.", ())),
        tools=tuple(ToolSpec(name=name, description="d", parameters={"type": "object"}) for name in tools),
        temperature=None,
        max_output_tokens=None,
        effort="medium",
        speed="standard",
        session_key="thr",
        cache_mark=None,
        window=None,
    )


def test_a_request_is_parted_by_section_group_and_role() -> None:
    parts: ContextParts = parts_of(
        _request(
            "You are Dot.\n# How to work\nPlan.\n# Trust and safety\nAsk.\n",
            ("read_file", "browser_open", "mcp__linear__find"),
        )
    )
    assert parts.system == {OPENING: 13, "How to work": 20, "Trust and safety": 24}
    assert set(parts.tools) == {"core", "browser", "mcp__linear"} and parts.tools_offered == 3
    assert parts.history == {"user": 6}
    assert parts.tokens > 0


def test_a_comment_in_a_fenced_block_is_no_section() -> None:
    parts: ContextParts = parts_of(_request("# Skills\n```\n# install deps\nnpm i\n```\n# Web\nRead.\n", ()))
    assert list(parts.system) == ["Skills", "Web"]


def test_stats_name_the_medians_and_the_largest_parts() -> None:
    def one(prompt: int, browser: int) -> str:
        return ContextParts(
            system={"How to work": prompt, "Web": 320},
            tools={"core": 6400, "browser": browser},
            tools_offered=40,
            history={"user": 64},
            tokens=4000,
        ).model_dump_json()

    lines: list[str] = render_context([one(3200, 3200), one(6400, 0), one(3200, 3200)])
    assert lines[1].startswith("What a run's first step carries (median of the newest 3 runs")
    assert lines[2] == "  in all 4,000"
    assert lines[3] == "  system prompt 1,100: How to work 1,000, Web 100"
    assert lines[4] == "  tools 3,000 (40 offered): core 2,000, browser 1,000"
    assert render_context([]) == ["", "What a run's first step carries: nothing recorded yet."]
