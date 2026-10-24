"""Whether a run's answer restates a text reply (item 620): the owner's chat of 2026-10-24 showed a screenshot's
description twice, as a reply and as the answer the finish wrote again in other words; a reply that says what the answer
does not (the review of item 567) stays."""

from dotagent.agent.restating import restates

# The owner's run, a reply and the finish after the loop's note, as their chat showed them (shortened to three lines).
_REPLY: str = """Ekran görüntüsünü yukarıda attım, bir kopyasını da kaydettim:
`/Users/dogan/Library/Application Support/Dot/workspace/screenshots/ekran-2026-10-24-1617.png`

Ekranda ne var:
- **Önde kod editörü (Nightly)** açık: "Dot / Web-research araç kuralları düzeltmesi" konuşması. Sağ panelde "main"
  dalı, commit/changes (+178), 3 çalışan agent ve önceki 56 agent'tan 3'ü failed görünüyor.
- **Sağ üstte** bir bildirim: "Sahibim — Kral ekranda ne görüyorsun mesela bir ekran fotoğrafı yollasana bana".
- Sağ altta **Mesajlar** uygulaması Dock'ta, üstte menü çubuğunda tarih/saat **Cmt 24.10. 16:17**, pil %81."""
_ANSWER: str = """Ekran görüntüsünü yukarıda attım, dosya olarak da kaydettim:
`/Users/dogan/Library/Application Support/Dot/workspace/screenshots/ekran-2026-10-24-1617.png` (2.6 MB, 1366x888).

Ekranda ne var:
- **Önde kod editörü (Nightly)**: "Dot / Web-research araç kuralları düzeltmesi" konuşması açık. Sağ panelde `main`
  dalı, "Changes +178 −6", 3 çalışan agent ve önceki 56 agent'tan "3 failed".
- **Sağ üstte** macOS bildirimi: "Sahibim — Kral ekranda ne görüyorsun mesela bir ekran fotoğrafı yollasana bana".
- **Menü çubuğu**: Cmt 24.10. 16:17, pil %81; Dock'ta Mesajlar uygulaması açık ve imleç onun üzerinde."""


def test_the_owners_screenshot_described_again_in_other_words_is_restated() -> None:
    assert restates(_REPLY, _ANSWER)


def test_a_reply_with_a_line_the_answer_left_out_is_not_restated() -> None:
    """The review of item 567: a warning the finish left out would be lost with the reply."""
    assert not restates(f"{_REPLY}\nAma yedekleme adımı başarısız oldu: disk dolu.", _ANSWER)
    assert not restates(
        "The prompt for the coding agent:\n1. Fix defect 1 in the search service.\n"
        "But the backup step failed: the disk is full.",
        "Here is the prompt you can give the coding agent:\n1. Fix defect 1 in the search service.",
    )


def test_an_answer_that_only_points_at_the_reply_does_not_restate_it() -> None:
    assert not restates(_REPLY, "Yukarıda.")
    assert not restates(_REPLY, "See the reply above for the description of the screen, with the file's path.")


def test_the_same_words_and_a_reworded_first_line_are_restated() -> None:
    body: str = "\n".join(f"{n}. Fix defect {n} in the search service and add a test." for n in range(1, 6))
    reworded: str = f"Here is the prompt you can give the coding agent:\n{body}"
    assert restates(f"The prompt for the coding agent, to paste as it is:\n{body}", reworded)
    assert restates(body, body)


def test_lines_of_marks_alone_and_blank_lines_ask_nothing() -> None:
    assert restates("Done.\n\n---\n", "Done: the files are in place.")


def test_a_number_or_a_not_the_answer_lost_keeps_the_reply() -> None:
    """The review of item 620: most of a line's words held is not enough when the answer lost what turns its sense."""
    assert not restates(
        "The backup did not finish before midnight on Friday.", "The backup finished before midnight on Friday."
    )
    assert not restates("The tests ran: 41 passed and 2 failed today.", "The tests ran: 41 passed and failed today.")
    assert not restates("Rapor dosyası klasörde yok, yeniden yazılmalı.", "Rapor dosyası klasörde, yeniden yazılmalı.")
    # The same number written another way, and a "not" kept, are held.
    assert restates("Saat 16:17, pil %81; dosya hâlâ yok.", "Pil 81%, saat 16.17 itibarıyla; dosya hâlâ yok.")
