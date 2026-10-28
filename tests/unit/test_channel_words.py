"""Dot's own lines on a channel in the owner's language (item 805): the language is told by the owner's messages and
kept, the setting can hold one, and every line has its Turkish."""

from dataclasses import fields

import pytest

from dotagent.channels.intake import Intake, Tell
from dotagent.channels.pictures import TOO_BIG, TOO_MANY, Found, Left
from dotagent.channels.progress import Progress, step_words
from dotagent.channels.router import ChannelRouter
from dotagent.channels.text import cut_with_link
from dotagent.channels.words import (
    ENGLISH,
    LANGUAGE_KEY,
    STEP_KEYS,
    TURKISH,
    LanguageSetting,
    SpokenLanguage,
    language_of,
)
from dotagent.store.models import EventRecord


class _Kept:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def kv_get(self, key: str) -> str | None:
        return self.values.get(key)

    async def kv_set(self, key: str, value: str) -> None:
        self.values[key] = value


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Bugün hava nasıl?", "tr"),
        ("bana masaüstündeki dosyaları göster", "tr"),
        ("ne var ne yok", "tr"),
        ("şu an", "tr"),
        ("What is on my calendar today?", "en"),
        ("please check the mail", "en"),
        ("https://example.com/a", None),
        ("42", None),
        ("ok", None),
    ],
)
def test_the_language_of_a_message_is_told_by_its_letters_and_common_words(text: str, language: str | None) -> None:
    assert language_of(text) == language


async def test_the_language_follows_the_owners_messages_and_is_kept() -> None:
    kept: _Kept = _Kept()
    setting: list[LanguageSetting] = ["auto"]
    spoken: SpokenLanguage = SpokenLanguage(kept, lambda: setting[0])
    await spoken.load()
    assert spoken.words() is ENGLISH
    await spoken.hear("Bunu yapar mısın?")
    assert spoken.language == "tr" and spoken.words() is TURKISH and kept.values[LANGUAGE_KEY] == "tr"
    # A message whose language cannot be told keeps the one before it.
    await spoken.hear("https://example.com")
    assert spoken.language == "tr"
    # After a restart it is read back.
    again: SpokenLanguage = SpokenLanguage(kept, lambda: setting[0])
    await again.load()
    assert again.language == "tr"
    # The setting holds one whatever the owner writes.
    setting[0] = "en"
    assert again.words() is ENGLISH


async def test_a_kept_value_that_is_no_language_is_not_taken() -> None:
    kept: _Kept = _Kept()
    kept.values[LANGUAGE_KEY] = "xx"
    spoken: SpokenLanguage = SpokenLanguage(kept, lambda: "auto")
    await spoken.load()
    assert spoken.language == "en"


def test_every_line_has_its_turkish_and_every_step_its_phrase() -> None:
    for field in fields(ENGLISH):
        english: object = getattr(ENGLISH, field.name)
        turkish: object = getattr(TURKISH, field.name)
        if isinstance(english, str):
            assert isinstance(turkish, str) and turkish, field.name
            # A template takes the same values in both.
            assert sorted(part.split("}")[0] for part in english.split("{")[1:]) == sorted(
                part.split("}")[0] for part in turkish.split("{")[1:]
            ), field.name
    assert set(ENGLISH.steps) == set(TURKISH.steps) == set(STEP_KEYS)


def test_intake_tells_in_the_owners_language() -> None:
    intake: Intake = Intake(lambda: TURKISH)
    told = intake.judge(None, 100.0, True, 100.0)
    assert told == Tell(TURKISH.attachment_only)
    old = intake.judge("eski", 0.0, False, 10_000.0)
    assert old == Tell(TURKISH.too_old)
    long = intake.judge("a" * 5000, 100.0, False, 100.0)
    assert isinstance(long, Tell) and "5000 karakter" in long.notice


def test_progress_speaks_turkish() -> None:
    progress: Progress = Progress(0.0, first_after_s=1.0, words=TURKISH)
    for tool, args in (
        ("read_file", {"path": "/tmp/notes.txt"}),
        ("web_fetch", {"url": "https://www.example.com/a?b=c"}),
        ("desktop_click", {}),
        ("shell", {"command": "ls ~/Desktop"}),
    ):
        said: str | None = step_words(tool, args, TURKISH)
        assert said is not None
        progress.step(said)
    message: str | None = progress.message(125.0)
    assert message == (
        "Çalışıyorum (2 dk 5 sn):\nnotes.txt dosyasını okudum\nbir sayfa okudum: www.example.com\n"
        "masaüstünü kullandım\n$ ls ~/Desktop"
    )


def test_the_routers_lines_speak_turkish() -> None:
    ended = EventRecord(id=1, run_id="r", ts=1.0, kind="run_finished", data={"status": "cancelled", "result": ""})
    assert ChannelRouter._answer(ended, False, TURKISH) == "Görev durduruldu."
    # The run's own English sentence of how it ended is said once, in Turkish.
    english = EventRecord(
        id=1, run_id="r", ts=1.0, kind="run_finished", data={"status": "cancelled", "result": "The run was stopped."}
    )
    assert ChannelRouter._answer(english, False, TURKISH) == "Görev durduruldu."
    done = EventRecord(id=1, run_id="r", ts=1.0, kind="run_finished", data={"status": "done", "result": ""})
    assert ChannelRouter._answer(done, True, TURKISH) == f"{TURKISH.unread_answer}\n\nTamam."
    waits = EventRecord(
        id=2,
        run_id="r",
        ts=1.0,
        kind="approval_requested",
        data={"summary": "Run rm -r build", "severity": "dangerous", "args": {}},
    )
    assert ChannelRouter._asking(waits, None, 1, TURKISH) == (
        "Dot onayını bekliyor: Run rm -r build (ve 1 tane daha)\nHepsini Dot'ta cevapla, Mac'inde; burada değil."
    )


def test_the_cut_and_the_left_out_line_speak_turkish() -> None:
    cut: str = cut_with_link("kelime " * 100, 80, None, TURKISH)
    assert cut.endswith("Cevabın devamı Mac'indeki Dot'ta.") and len(cut) <= 80
    found: Found = Found("x", (), (Left("big.png", TOO_BIG, None), Left("a.png", TOO_MANY, None)))
    assert (
        found.note(TURKISH) == "Gönderilmeyenler: big.png (20 MB'tan büyük), 1 tane daha (bir mesajda en çok 4 tane)."
    )
