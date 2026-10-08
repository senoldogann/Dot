"""Which words of a query are used to find notes, on fixed sentences."""

import pytest

from dotagent.memory.words import lowercase, search_terms


@pytest.mark.parametrize(
    ("text", "terms"),
    [
        (
            "What is the name of the wifi network at the user's home?",
            ("name", "wifi", "network", "user", "home"),
        ),
        ("Bu ağın adı ne?", ("agin", "adi")),
        ("Işık ışık ISIK", ("isik",)),
        ("şu su", ("su",)),
        ("dog cat dog Dog", ("dog", "cat")),
        ("a b c wifi", ("wifi",)),
        ("İstanbul istanbul İSTANBUL", ("istanbul",)),
        ("this is it and that is the", ()),
        ("", ()),
        ("?! ... --", ()),
    ],
)
def test_a_query_keeps_only_the_words_that_tell_notes_apart(text: str, terms: tuple[str, ...]) -> None:
    assert search_terms(text) == terms


def test_a_long_query_is_cut_to_its_first_words() -> None:
    terms: tuple[str, ...] = search_terms(" ".join(f"word{number}" for number in range(40)))
    assert terms == tuple(f"word{number}" for number in range(24))


def test_the_turkish_dotted_capital_i_becomes_a_plain_i() -> None:
    assert lowercase("ŞEKER İÇİN") == "şeker için"
    assert lowercase("İstanbul") == "istanbul"
    assert "̇" not in lowercase("İİİ")
