"""Which handed notes a run's words echo (item 1715): a pure, rough signal for audits."""

from dotagent.memory.echo import echoed_notes
from dotagent.store.models import MemoryRecord


def _note(identifier: str, content: str) -> MemoryRecord:
    return MemoryRecord(
        id=identifier,
        content=content,
        tags=(),
        created_at=1.0,
        updated_at=1.0,
        source_run=None,
        agent_id=None,
        used_count=0,
        last_used_at=None,
        expires_at=None,
        replaced_by=None,
        tainted=False,
    )


INVOICES: MemoryRecord = _note("mem_invoices", "The owner's invoices live in Documents/Invoices, one folder a year.")
TURKISH: MemoryRecord = _note("mem_turkish", "Toplantı notları Pazartesi sabahı gönderilir.")
SHORT: MemoryRecord = _note("mem_short", "Use uv.")


def test_two_distinctive_words_or_the_id_echo_a_note() -> None:
    said: str = 'Moved the file into Documents/Invoices/2026 {"path": "~/Documents/Invoices"}'
    assert echoed_notes([INVOICES, TURKISH], said) == ("mem_invoices",)
    # One distinctive word is not enough for a note that has two or more.
    assert echoed_notes([INVOICES], "I opened the invoices.") == ()
    assert echoed_notes([TURKISH], "toplanti notlari gönderildi") == ("mem_turkish",)
    assert echoed_notes([TURKISH], "Read mem_turkish first.") == ("mem_turkish",)


def test_a_note_with_no_distinctive_word_is_echoed_by_its_id_alone() -> None:
    assert echoed_notes([SHORT], "Use uv to install it.") == ()
    assert echoed_notes([SHORT], "Followed mem_short.") == ("mem_short",)
