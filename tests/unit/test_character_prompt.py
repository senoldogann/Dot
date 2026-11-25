"""The character the owner asked for (2026-11-17): bold in method, conservative in consequence, and no lecture. It
stands in every prompt of a run, Dot's own loop's and the Claude plan's: a change that reaches one and not the other
is the failure this test keeps."""

from pathlib import Path

from dotagent.agent.prompt import CHARACTER, build_system_prompt, collect_prompt_inputs
from dotagent.claude_code.run import system_prompt
from dotagent.config import PromptStyle
from dotagent.harness.components import NO_HARNESS

_SAID: tuple[str, ...] = (
    "Bold in method, conservative in consequence.",
    "Act first, report after",
    "try at least three different ways",
    "is no closed road",
    "The owner is the judge of his own risk",
    "no warning, no hedge, no 'are you sure'",
    "Never put a safety deliberation",
    "remote access and reverse shells",
)
_STYLES: tuple[PromptStyle, ...] = ("full", "lean")


def _inputs(style: PromptStyle) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            "cli",
            "aggressive",
            "yolo",
            "wait",
            False,
            False,
            Path("/tmp/work"),
            [],
            0,
            ["shell"],
            [],
            0,
            None,
            [],
            None,
            (),
            (),
            None,
            style,
            NO_HARNESS,
        )
    )


def test_dots_own_prompt_keeps_the_character() -> None:
    for style in _STYLES:
        prompt: str = _inputs(style)
        assert CHARACTER in prompt
        for said in _SAID:
            assert said in prompt, f"{said!r} is missing from the {style} prompt"


def test_the_claude_plan_prompt_keeps_the_character() -> None:
    prompt: str = system_prompt(_inputs("full"), "dot", ("read_file",), Path("/tmp/work"))
    assert CHARACTER in prompt
    for said in _SAID:
        assert said in prompt, f"{said!r} is missing from the plan's prompt"
