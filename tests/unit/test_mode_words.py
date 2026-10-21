"""The mode a message's words ask for (item 495): research for an ask to only look, in English or in Turkish, with
the Turkish letters or without them; a task for anything else, a change to avoid among others included; and the mode
the owner names is the mode."""

import pytest

from dotagent.agent.mode_words import only_looks, resolved_mode
from dotagent.store.models import RunMode


@pytest.mark.parametrize(
    ("goal", "mode"),
    [
        ("Read-only: find out why the build is slow", "research"),
        ("Look at the logs but don't change anything", "research"),
        ("just research the best laptops under 1500 euros", "research"),
        ("Sadece araştır: en iyi telefon hangisi?", "research"),
        ("Projeyi incele, hiçbir şeyi değiştirme", "research"),
        ("hicbir seyi degistirmeden raporla", "research"),
        ("Salt okunur bak ve özetle", "research"),
        ("Fix the failing test", "task"),
        ("Don't change the colours, only the font", "task"),
        ("Sadece araştırıp düzelt", "task"),
        ("Merhaba, nasılsın?", "task"),
    ],
)
def test_auto_reads_research_only_from_an_ask_to_only_look(goal: str, mode: RunMode) -> None:
    assert resolved_mode("auto", goal) == mode


def test_a_mode_named_is_the_mode_and_the_words_found_are_cut_as_written() -> None:
    assert resolved_mode("task", "Read-only please") == "task"
    assert resolved_mode("chat", "sadece araştır") == "chat"
    assert only_looks("Lütfen SADECE ARAŞTIR ve yaz") == "SADECE ARAŞTIR"
    assert only_looks("İncele ama HİÇBİR ŞEYİ DEĞİŞTİRME") == "HİÇBİR ŞEYİ DEĞİŞTİRME"
