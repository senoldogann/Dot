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
        ("Don’t stop until every link works.", "Don’t stop"),
        ("Never stop now, the end is near.", "Never stop"),
        ("Translate the files without stopping.", "without stopping"),
        ("Rename the photos until everything is finished.", "until everything is finished"),
        ("Keep at it until it’s done.", "until it’s done"),
        ("Profili geliştir, hiç durma.", "hiç durma"),
        ("hic durma, devam et", "hic durma"),
        ("HİÇ DURMA", "HİÇ DURMA"),
        ("Durma!", "Durma"),
        ("Hepsini bitirmeden durma\nSonra bana yaz.", "durma"),
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
        "Don't stop the server while you update it.",
        "Find non-stop flights to Tokyo.",
        "Wait until it's done, then read the log.",
        "Kurulum bitene kadar bekle.",
        "Durmadan önce dosyayı kaydet.",
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
