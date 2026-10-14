"""The message that tells a chat that its command in the background ended (item 229): what Dot saw, on its first line,
and nothing the command printed; and how long a while lasted, in words."""

from dotagent.core.text import duration_text
from dotagent.terminal.session import ended_notice


def test_a_while_reads_in_seconds_minutes_or_hours() -> None:
    assert [duration_text(seconds) for seconds in (0.4, 45, 59.6, 754, 3600, 34_870)] == [
        "0 s",
        "45 s",
        "1 min",
        "12 min",
        "1 h 0 min",
        "9 h 41 min",
    ]


def test_the_notice_names_the_session_its_exit_its_time_and_the_command_on_its_first_line() -> None:
    first, then = ended_notice("t2", "uv run dotx eval\n  --repeat 3", 1, 34_870).split("\n")
    assert first == "The command in the background t2 ended with exit 1 after 9 h 41 min: uv run dotx eval --repeat 3"
    assert then == "Read what it printed with terminal_read t2, then go on with what it was started for."
    # A long command is cut in its middle.
    assert len(ended_notice("t1", "echo " + "x" * 1000, 0, 5).split("\n")[0]) < 400
