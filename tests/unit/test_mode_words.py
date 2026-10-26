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
        # An ask to be read-only is research, in whatever way it is put (item 691).
        ("Please stay read-only while you look into the build", "research"),
        ("Work read-only on this repository and report", "research"),
        ("Keep it read-only and tell me what you find", "research"),
        ("Run in read-only mode and list the failing tests", "research"),
        ("Fix nothing yet. Read-only, just tell me what is wrong", "research"),
        ("Be read-only: look at the logs", "research"),
        ("Projeyi salt okunur modda incele", "research"),
        ("Salt okunur kal ve loglara bak", "research"),
        ("Lütfen salt okunur çalış", "research"),
        ("Salt okunur calis, dosyalara dokunma", "research"),
        ("Salt okunur olarak incele", "research"),
        ("Salt okunur: build neden yavaş?", "research"),
        # A task that makes or names something read-only is a task (item 691).
        ("Make config.toml read-only", "task"),
        # Kept or staying read-only after a task names the thing is about the thing (the review of item 691).
        ("Copy prod.db to backup.db and keep it read-only", "task"),
        # An ask to the run joined to the task before it is research (the code review of item 691).
        ("Look into why the build is slow and stay read-only", "research"),
        ("Investigate the crash, work read-only", "research"),
        ("Look at the logs. Stay read-only.", "research"),
        ("Investigate the crash; work read-only", "research"),
        ("Investigate the crash — work read-only", "research"),
        ("Look into it (stay read-only)", "research"),
        ("Investigate the crash and keep things read-only", "research"),
        ("Read the logs, then run read-only", "research"),
        # After a task names a thing, "keep it", "run" and "go" read-only are about the thing, and a noun after
        # "read-only" makes it the thing's adjective.
        ("Copy prod.db to backup.db. Keep it read-only.", "task"),
        ("Run read-only checks on the repo. Then fix what fails.", "task"),
        ("Stay read-only replicas aside and resize the primary", "task"),
        ("The prod DB must stay read-only; add the index to the replica", "task"),
        ("Create a read-only API key for the dashboard", "task"),
        ("Give the new hire read-only access to the repo", "task"),
        ("Set the file to read-only and commit it", "task"),
        ("Turn on read-only mode in the app's settings", "task"),
        ("Add a read-only flag to the export command", "task"),
        ("Dosyayı salt okunur yap", "task"),
        ("config.toml dosyasını salt okunur olarak ayarla", "task"),
        ("Salt okunur bir API anahtarı oluştur", "task"),
        ("Klasörü salt okunur modda bağla", "task"),
        ("Salt okunur modda kal", "research"),
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
