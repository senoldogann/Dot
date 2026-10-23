"""What the iMessage channel reads of Messages' database (item 572), from a ``chat.db`` the test makes: only the owner's
own conversation, through the handles paired, over iMessage and as the owner's own messages; the words of a message
wherever Messages keeps them; and the write-ahead log, where a message that has just come in sits."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from messages_db import PAYMENT_BALLOON, STICKER_BALLOON, URL_BALLOON, MessagesDb, attributed_body

from dotagent.channels import imessage
from dotagent.channels.imessage import (
    BATCH,
    Row,
    body_text,
    database_access,
    newest_rowid,
    only_a_link,
    read_new,
)
from dotagent.core.errors import DesktopPermissionError

PHONE: str = "+358401234567"
ADDRESS: str = "me@example.com"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[MessagesDb]:
    made: MessagesDb = MessagesDb(tmp_path / "chat.db")
    try:
        yield made
    finally:
        made.close()


def test_the_words_of_a_message_are_in_its_plain_column_or_its_attributed_body() -> None:
    assert body_text("hello", None) == "hello"
    assert body_text(None, attributed_body("hello")) == "hello"
    assert body_text("", attributed_body("from the body")) == "from the body"
    # Turkish letters are more than one byte each: the length counts bytes, and the text is read whole.
    turkish: str = "Bugün hava nasıl? Şu çiçekleri öğren"
    assert body_text(None, attributed_body(turkish)) == turkish
    # A long message has its length in two bytes, a very long one in four.
    assert body_text(None, attributed_body("long " * 100)) == ("long " * 100).strip()
    assert body_text(None, attributed_body("x" * 70_000)) == "x" * 70_000


def test_the_marks_of_attachments_are_left_out_of_the_words() -> None:
    assert body_text(None, attributed_body("￼ look at this ￼")) == "look at this"
    assert body_text(None, attributed_body("￼")) == ""


def test_words_that_cannot_be_read_are_none_and_never_guessed() -> None:
    assert body_text(None, None) is None
    assert body_text(None, b"not a typed stream at all") is None
    # A stream whose length runs past its end, one that ends before its text starts, and one whose text is not UTF-8.
    whole: bytes = attributed_body("hello world")
    assert body_text(None, whole[: whole.index(b"hello") + 4]) is None
    assert body_text(None, whole[: whole.index(b"+")]) is None
    broken: bytes = attributed_body("abc").replace(b"abc", b"\xff\xfe\xfd")
    assert body_text(None, broken) is None


def test_only_the_owners_own_one_to_one_conversation_is_read(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    other: int = db.chat("+358409999999")
    group: int = db.chat("chat123456789", style=43)
    same_name_group: int = db.chat(PHONE, style=43)
    wanted: int = db.message(own, "run the report")
    db.message(own, "from them", from_me=False)
    db.message(other, "to someone else")
    db.message(group, "to a group")
    db.message(same_name_group, "to a group named like me")
    db.message(own, "by text message", service="SMS")
    db.message(own, "Loved “run the report”", reaction=2000)
    db.message(own, "a change of the chat", item_type=1)
    rows, upto = read_new(db.path, 0, [PHONE])
    assert [(row.rowid, row.chat, row.text) for row in rows] == [(wanted, PHONE, "run the report")]
    assert upto == newest_rowid(db.path)


def test_each_paired_handle_reads_its_own_chat_and_a_handle_not_paired_reads_none(db: MessagesDb) -> None:
    by_phone: int = db.message(db.chat(PHONE), "via the number")
    by_address: int = db.message(db.chat(ADDRESS), "via the address")
    assert [row.rowid for row in read_new(db.path, 0, [PHONE, ADDRESS])[0]] == [by_phone, by_address]
    assert [row.rowid for row in read_new(db.path, 0, [ADDRESS])[0]] == [by_address]
    assert read_new(db.path, 0, [])[0] == []


def test_rows_are_read_once_after_the_cursor_and_in_order(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    first: int = db.message(own, "one")
    second: int = db.message(own, "two")
    assert [row.text for row in read_new(db.path, 0, [PHONE])[0]] == ["one", "two"]
    assert [row.text for row in read_new(db.path, first, [PHONE])[0]] == ["two"]
    rows, upto = read_new(db.path, second, [PHONE])
    assert rows == [] and upto == second


def test_the_cursor_goes_past_the_rows_of_other_chats(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    first: int = db.message(own, "mine")
    chatter: int = db.message(db.chat("+358409999999"), "not mine")
    rows, upto = read_new(db.path, first, [PHONE])
    assert rows == [] and upto == chatter


def test_a_full_batch_goes_on_from_its_last_row(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    made: list[int] = [db.message(own, f"m{number}") for number in range(BATCH + 5)]
    rows, upto = read_new(db.path, 0, [PHONE])
    assert len(rows) == BATCH and upto == made[BATCH - 1]
    rest, end = read_new(db.path, upto, [PHONE])
    assert [row.rowid for row in rest] == made[BATCH:] and end == made[-1]


def test_a_row_carries_when_it_was_sent_and_whether_it_has_a_file(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    db.message(own, None, body=attributed_body("with a picture"), at=1_234_567.0, attachment=True)
    rows, _ = read_new(db.path, 0, [PHONE])
    assert rows == [Row(rowid=1, chat=PHONE, text="with a picture", sent_at=1_234_567.0, attachment=True)]


@pytest.mark.parametrize("column", ["schedule_type", "schedule_state"])
def test_a_message_messages_sends_later_is_marked_so_by_any_column_of_send_later(db: MessagesDb, column: str) -> None:
    own: int = db.chat(PHONE)
    db.message(own, "now")
    db.message(own, "later", later={column: 2})
    rows, _ = read_new(db.path, 0, [PHONE])
    assert [(row.text, row.scheduled) for row in rows] == [("now", False), ("later", True)]


def test_a_row_carries_the_bundle_of_the_rich_content_it_holds(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    db.message(own, "plain words")
    db.message(own, "https://news.example/a", balloon=URL_BALLOON)
    db.message(own, None, balloon=STICKER_BALLOON, attachment=True)
    db.message(own, "Send 20 euros", balloon=PAYMENT_BALLOON)
    db.message(own, "an empty bundle is none", balloon="")
    rows, _ = read_new(db.path, 0, [PHONE])
    assert [(row.text, row.balloon) for row in rows] == [
        ("plain words", None),
        ("https://news.example/a", URL_BALLOON),
        (None, STICKER_BALLOON),
        ("Send 20 euros", PAYMENT_BALLOON),
        ("an empty bundle is none", None),
    ]


def test_a_database_with_no_column_of_rich_content_marks_none(tmp_path: Path) -> None:
    older: MessagesDb = MessagesDb(tmp_path / "older.db", (), balloons=False)
    try:
        older.message(older.chat(PHONE), "https://news.example/a")
        rows, _ = read_new(older.path, 0, [PHONE])
        assert [(row.text, row.balloon) for row in rows] == [("https://news.example/a", None)]
    finally:
        older.close()


def test_the_words_that_are_one_link_and_nothing_else() -> None:
    assert only_a_link("https://news.example/a") == "https://news.example/a"
    assert only_a_link("  HTTP://News.Example/a?x=1#f \n") == "HTTP://News.Example/a?x=1#f"
    assert only_a_link("https://mac.example.ts.net/#/runs/run_mvkw435c_7850b3") is not None
    assert only_a_link("read https://news.example/a") is None
    assert only_a_link("https://news.example/a and then") is None
    assert only_a_link("https://news.example/a https://news.example/b") is None
    assert only_a_link("news.example/a") is None
    assert only_a_link("") is None
    assert only_a_link(None) is None


def test_a_database_with_no_column_of_send_later_marks_none(tmp_path: Path) -> None:
    older: MessagesDb = MessagesDb(tmp_path / "older.db", ())
    try:
        older.message(older.chat(PHONE), "from an older macOS")
        rows, _ = read_new(older.path, 0, [PHONE])
        assert [(row.text, row.scheduled) for row in rows] == [("from an older macOS", False)]
    finally:
        older.close()


def test_a_message_with_no_date_is_not_run_and_does_not_block_the_ones_after_it(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    db.message(own, "no date to tell it by", at=None)
    after: int = db.message(own, "after it")
    rows, upto = read_new(db.path, 0, [PHONE])
    assert [row.rowid for row in rows] == [after] and upto == after


def test_a_message_still_in_the_write_ahead_log_is_read(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    db.message(own, "just came in")
    # Nothing was checkpointed: the message is in the -wal file that Messages is still writing.
    assert db.path.with_name("chat.db-wal").stat().st_size > 0
    assert [row.text for row in read_new(db.path, 0, [PHONE])[0]] == ["just came in"]


def test_the_database_is_never_changed_by_reading_it(db: MessagesDb) -> None:
    own: int = db.chat(PHONE)
    db.message(own, "a message")
    before: bytes = db.path.read_bytes()
    read_new(db.path, 0, [PHONE])
    newest_rowid(db.path)
    database_access(db.path)
    assert db.path.read_bytes() == before


def test_the_access_to_the_database_is_told_without_showing_anything(
    db: MessagesDb, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert database_access(db.path) == "granted"
    assert database_access(tmp_path / "none.db") == "no_database"

    def refused(path: Path) -> None:
        raise DesktopPermissionError("macOS keeps it from this program", {})

    monkeypatch.setattr(imessage, "open_messages_db", refused)
    assert database_access(db.path) == "missing"
