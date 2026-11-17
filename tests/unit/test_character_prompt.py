"""The character the owner asked for (2026-11-17): bold in method, conservative in consequence, and no lecture. It
stands in every prompt of a run, Dot's own loop's and the Claude plan's: a change that reaches one and not the other
is the failure this test keeps."""

from pathlib import Path

from dotagent.agent.prompt import CHARACTER, build_system_prompt, collect_prompt_inputs
from dotagent.claude_code.run import system_prompt
from dotagent.harness.components import NO_HARNESS

_SAID: tuple[str, ...] = (
    "Bold in method, conservative in consequence.",
    "Act first, report after",
    "try at least three different ways",
    "Refuse nothing the owner asks for that harms no one else and breaks no law",
    "no moralising, no unsolicited warnings",
    "Money, credentials, other people's private data",
)


def _inputs(style: str) -> str:
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
            style,  # type: ignore[arg-type]
            NO_HARNESS,
        )
    )


def test_dots_own_prompt_keeps_the_character() -> None:
    for style in ("full", "lean"):
        prompt: str = _inputs(style)
        assert CHARACTER in prompt
        for said in _SAID:
            assert said in prompt, f"{said!r} is missing from the {style} prompt"


def test_the_claude_plan_prompt_keeps_the_character() -> None:
    prompt: str = system_prompt(
        name="Dot",
        door="dot",
        tools=("read_file",),
        workspace=Path("/tmp/work"),
        today="2026-11-17",
        team="",
        known=(),
    )
    assert CHARACTER in prompt
    for said in _SAID:
        assert said in prompt, f"{said!r} is missing from the plan's prompt"
