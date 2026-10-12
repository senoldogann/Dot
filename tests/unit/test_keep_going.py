"""The words of a goal that ask its run to keep going until it is done (item 132): asks in English and in Turkish,
with the Turkish letters and without them, and lookalikes that ask nothing of the kind."""

import pytest

from dotagent.agent.keep_going import keep_going_ask


@pytest.mark.parametrize(
    ("goal", "words"),
    [
        ("Clean the inbox and don't stop until it is done.", "don't stop"),
        ("Do not stop before the report is written.", "Do not stop"),
        ("Keep going until every link works.", "Keep going"),
        ("Fix the tests, never stop.", "never stop"),
        ("Work non-stop on the migration.", "non-stop"),
        ("Translate the files without stopping.", "without stopping"),
        ("Rename the photos until everything is finished.", "until everything is finished"),
        ("Profili geliştir, hiç durma.", "hiç durma"),
        ("hic durma, devam et", "hic durma"),
        ("HİÇ DURMA", "HİÇ DURMA"),
        ("Durma!", "Durma"),
        ("Durma, sonuna kadar git.", "Durma"),
        ("Hepsini bitirmeden durma", "durma"),
        ("Sakın durma, devam et.", "Sakın durma"),
        ("Durmadan devam et.", "Durmadan"),
        ("Durmayın, sonuna kadar gidin.", "Durmayın"),
        ("Durmak yok, bitir şunu.", "Durmak yok"),
        ("Testler geçene kadar uğraş, bitene kadar çalış.", "bitene kadar"),
        ("BİTENE KADAR çalış", "BİTENE KADAR"),
        ("İşi bitirene kadar devam et.", "bitirene kadar"),
        ("Liste tamamlanana kadar ekle.", "tamamlanana kadar"),
        ("Liste tamamlanincaya kadar ekle.", "tamamlanincaya kadar"),
    ],
)
def test_a_goal_that_asks_to_keep_going_gives_its_words_as_written(goal: str, words: str) -> None:
    assert keep_going_ask(goal) == words


@pytest.mark.parametrize(
    "goal",
    [
        "Stop the server on port 8080.",
        "Find the nearest bus stop.",
        "Wait until it builds, then read the log.",
        "Devam et.",
        "Durmak istiyorum, yarın bakarız.",
        "Saatin durması normal mi?",
        "Videonun durma noktasını bul.",
        "Motorun durma süresini ölç.",
        "Durum nedir?",
        "Oturma odasının ışığını kapat.",
    ],
)
def test_a_goal_that_does_not_ask_to_keep_going_gives_none(goal: str) -> None:
    assert keep_going_ask(goal) is None
