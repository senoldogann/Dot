"""What a terminal session makes of a program's output, and of what is typed into it."""

from dotagent.llm.types import ToolSpec
from dotagent.policy.shell import typed_lines
from dotagent.terminal.session import asks_for_secret, plain_text
from dotagent.tools.shell import BACKGROUND_HINT, build_shell_tool
from dotagent.tools.terminal import build_terminal_tools, chatless_specs


def test_escape_sequences_leave_the_output_and_line_ends_become_newlines() -> None:
    raw: str = "\x1b[1;32mok\x1b[0m done\r\n\x1b]0;title\x07>>> \x1b[K10%\r20%\r\n"
    assert plain_text(raw) == "ok done\n>>> 10%20%\n"


def test_a_password_prompt_counts_only_as_the_last_line() -> None:
    assert asks_for_secret("Connecting...\r\nuser@host's password: ")
    assert asks_for_secret("Enter passphrase for key '/Users/me/.ssh/id_ed25519':")
    assert asks_for_secret("[sudo] password for me:")
    assert not asks_for_secret("Password: \r\nWelcome!\r\n$ ")
    assert not asks_for_secret("Reset your password at https://example.com\r\n>>> ")


def test_the_lines_typing_completes_start_with_what_was_typed_before_on_the_line() -> None:
    assert typed_lines("", "6*7\n") == (("6*7",), "")
    assert typed_lines("rm -rf ", "build\nls") == (("rm -rf build",), "ls")
    assert typed_lines("half", " a line") == ((), "half a line")
    assert typed_lines("", "a\r\nb\rc\n") == (("a", "b", "c"), "")


def test_keys_that_edit_the_line_count_and_keys_that_cannot_be_followed_leave_a_line_that_cannot_be_told() -> None:
    assert typed_lines("echo safe", "\x15rm -rf ~/x\n") == (("rm -rf ~/x",), "")
    assert typed_lines("", "ls -l\x7fa\n") == (("ls -a",), "")
    assert typed_lines(None, "\x03ls\n") == (("ls",), "")
    assert typed_lines(None, "ls\n") == ((None,), "")
    assert typed_lines("rm -rf ~/x", "\x0f") == ((None,), None)
    assert typed_lines("", "cat ~/.ss\t") == ((), None)
    assert None in typed_lines("", "echo rm -rf ~/x\x01\x1b[3~\n")[0]
    assert None in typed_lines("", "\x1b[A\n")[0]


def test_a_run_no_chat_is_told_in_is_offered_no_command_in_the_background() -> None:
    """Item 1760: delegated runs were offered terminal_open's background, set it, and were refused, 22 times."""
    specs: tuple[ToolSpec, ...] = (
        build_shell_tool().spec,
        *(tool.spec for tool in build_terminal_tools()),
    )
    offered: dict[str, ToolSpec] = {spec.name: spec for spec in chatless_specs(specs)}
    assert BACKGROUND_HINT in specs[0].description
    assert "background" in str(specs[1].parameters["properties"])
    assert "background" not in str(offered["terminal_open"].parameters)
    assert "terminal_open with background" not in offered["shell"].description
    assert offered["shell"].description.endswith("never with a launchd job of your own.")
    assert offered["terminal_send"] == specs[2]
