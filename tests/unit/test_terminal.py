"""What a terminal session makes of a program's output, and of what is typed into it."""

from dotagent.terminal.session import asks_for_secret, plain_text, typed_lines


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
