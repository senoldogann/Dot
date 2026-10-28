"""What a channel does with a message of the owner before the router sees it (item 572): the limits that hold for every
channel. Words are read; a message too old, too long, with only a file, or past thirty in a minute is told why once or
let go, and one whose words cannot be read is never answered."""

import pytest

from dotagent.channels.intake import (
    FLOOD,
    MAX_IN_CHARS,
    MAX_PER_MINUTE,
    STALE_S,
    TOO_OLD,
    UNREAD_NOTE,
    Accept,
    Intake,
    Skip,
    Tell,
    too_long,
)

NOW: float = 1_000_000.0


def test_words_are_read_as_they_are_stripped() -> None:
    assert Intake().judge("  check the weather \n", NOW - 5, False, NOW) == Accept("check the weather")


def test_a_message_whose_words_cannot_be_read_is_never_answered() -> None:
    # It may be one of Dot's own, which the owner's chat holds too: a notice about it would be answered by itself.
    assert isinstance(Intake().judge(None, NOW, False, NOW), Skip)


def test_a_message_with_a_file_is_accepted_for_the_channel_to_read_its_files() -> None:
    """Item 806: the files are read by the channel after the intake, so a message of a file alone is accepted with no
    words, and one with words keeps them as they are; the channel adds the notes."""
    assert Intake().judge(None, NOW, True, NOW) == Accept("")
    assert Intake().judge("   ", NOW, True, NOW) == Accept("")
    assert Intake().judge("what is wrong with this screenshot?", NOW, True, NOW) == Accept(
        "what is wrong with this screenshot?"
    )
    assert "Do not guess" in UNREAD_NOTE
    # Words with no file are the words, whatever is in them.
    assert Intake().judge("what is wrong with this screenshot?", NOW, False, NOW) == Accept(
        "what is wrong with this screenshot?"
    )


@pytest.mark.parametrize("word", ["/stop", "stop", "Dur", "durdur"])
def test_a_stop_word_sent_with_a_file_is_still_only_the_stop(word: str) -> None:
    assert Intake().judge(word, NOW, True, NOW) == Accept(word)


def test_the_limit_counts_the_owners_words() -> None:
    assert Intake().judge("x" * MAX_IN_CHARS, NOW, True, NOW) == Accept("x" * MAX_IN_CHARS)
    assert Intake().judge("x" * (MAX_IN_CHARS + 1), NOW, True, NOW) == Tell(too_long(MAX_IN_CHARS + 1))


def test_an_empty_message_with_no_file_is_let_go() -> None:
    assert isinstance(Intake().judge("   ", NOW, False, NOW), Skip)


def test_a_message_that_came_in_long_ago_is_not_run() -> None:
    assert Intake().judge("do it", NOW - STALE_S + 1, False, NOW) == Accept("do it")
    assert Intake().judge("do it", NOW - STALE_S - 1, False, NOW) == Tell(TOO_OLD)


def test_a_message_over_four_thousand_characters_is_told_so_and_not_cut() -> None:
    assert Intake().judge("x" * MAX_IN_CHARS, NOW, False, NOW) == Accept("x" * MAX_IN_CHARS)
    verdict = Intake().judge("x" * (MAX_IN_CHARS + 1), NOW, False, NOW)
    assert verdict == Tell(too_long(MAX_IN_CHARS + 1))
    assert "4000" in too_long(MAX_IN_CHARS + 1)


def test_thirty_messages_a_minute_are_read_and_the_flood_after_them_is_told_of_once() -> None:
    intake: Intake = Intake()
    for second in range(MAX_PER_MINUTE):
        assert isinstance(intake.judge(f"m{second}", NOW, False, NOW + second), Accept)
    assert intake.judge("one more", NOW, False, NOW + 31) == Tell(FLOOD)
    # The rest of the flood gets no answer, so that telling it does not add to it.
    for second in range(32, 40):
        assert isinstance(intake.judge("again", NOW, False, NOW + second), Skip)
    # A minute after the first, messages are read again.
    assert isinstance(intake.judge("later", NOW, False, NOW + 61), Accept)


def test_a_stop_is_read_through_a_flood() -> None:
    """Item 683: a run that floods the chat is what a stop is sent to stop; the flood's limit kept the stop out."""
    intake: Intake = Intake()
    for second in range(MAX_PER_MINUTE + 3):
        intake.judge(f"m{second}", NOW, False, NOW + second)
    assert intake.judge("/stop", NOW + 34, False, NOW + 34) == Accept("/stop")
    assert intake.judge("Dur", NOW + 35, False, NOW + 35) == Accept("Dur")
    assert isinstance(intake.judge("one more", NOW + 36, False, NOW + 36), Skip)
