"""The words that stop the run at work in a channel's chat (item 611): ``/stop`` and a few plain words, as a whole
message and nothing more, in any case. The owner writes in English and in Turkish."""

import pytest

from dotagent.channels.types import STOP_COMMAND, STOP_WORDS, is_stop


@pytest.mark.parametrize(
    "text",
    [
        "/stop",
        "/STOP",
        " /Stop ",
        "stop",
        "Stop",
        "STOP",
        "dur",
        "Dur",
        "DUR",
        "durdur",
        "Durdur",
        "DURDUR",
        "stop.",
        "Stop!",
        "dur!!",
        "  stop \n",
    ],
)
def test_a_message_that_is_only_a_stop_word_stops_the_run(text: str) -> None:
    assert is_stop(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "!",
        ".",
        "stop it",
        "please stop",
        "do not stop",
        "stopping",
        "stop, then start again",
        "stop the music in the other room",
        "/stop now",
        "/stopp",
        "s top",
        "dur mu",
        "durdurma",
        "what does stop mean?",
    ],
)
def test_a_sentence_that_holds_a_stop_word_is_not_a_stop(text: str) -> None:
    assert not is_stop(text)


def test_the_set_of_stop_words_is_small_and_exact() -> None:
    assert STOP_COMMAND == "/stop"
    assert frozenset({"/stop", "stop", "dur", "durdur"}) == STOP_WORDS
