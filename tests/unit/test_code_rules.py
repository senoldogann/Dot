"""The pure parts of reading one-time codes (item 31): which number of a message is the code, the Mail script and its
answer, and the Messages database read on a small copy of its schema."""

import sqlite3
import time
from pathlib import Path

import pytest

from dotagent.core.errors import DesktopPermissionError
from dotagent.desktop.codes import FoundMessage, extract_code, mail_script, parse_mail_result, read_messages

_APPLE_EPOCH: float = 978_307_200.0


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Your verification code is 482913. It expires in 10 minutes.", "482913"),
        ("Code: 7731", "7731"),
        ("G-583920 is your Google verification code.", "583920"),
        ("248 113 is your Instagram code", "248113"),
        ("Doğrulama kodunuz: 90210", "90210"),
        ("Use 55-66-77 to sign in", None),
        ("Order 20261010 shipped; call 555 0199. Your code expires at 10:30.", None),
        ("We noticed a login from a new device on 2026-10-10.", None),
        ("Hello 123456", "123456"),
        ("Hello 123456 and 654321", None),
    ],
)
def test_the_code_is_the_number_after_a_cue_or_the_only_six_digit_number(text: str, code: str | None) -> None:
    assert extract_code(text) == code


def test_the_mail_script_quotes_the_filters_and_asks_for_the_newest_message() -> None:
    script: str = mail_script('no-reply@x.com" or 1', 'Your "code"', 5)
    assert 'sender contains "no-reply@x.com\\" or 1"' in script
    assert 'subject contains "Your \\"code\\""' in script
    assert "(current date) - (5 * minutes)" in script
    assert "sender contains" not in mail_script("", "", 5)


def test_the_answer_of_mail_is_age_sender_subject_and_content() -> None:
    found: FoundMessage | None = parse_mail_result("42\nShop <no-reply@shop.com>\nYour code\nCode: 1234\nBye")
    assert found == FoundMessage(
        sender="Shop <no-reply@shop.com>", subject="Your code", age_s=42.0, text="Code: 1234\nBye"
    )
    assert parse_mail_result("") is None


def _chat_db(path: Path, rows: list[tuple[float, str, str | None, bytes | None, int]]) -> None:
    connection: sqlite3.Connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);"
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, handle_id INTEGER, text TEXT, "
        "attributedBody BLOB, is_from_me INTEGER);"
    )
    for number, (date, handle, text, body, from_me) in enumerate(rows, start=1):
        connection.execute("INSERT INTO handle VALUES (?, ?)", (number, handle))
        connection.execute("INSERT INTO message VALUES (?, ?, ?, ?, ?, ?)", (number, date, number, text, body, from_me))
    connection.commit()
    connection.close()


def test_messages_are_read_newest_first_within_the_window_from_the_sender(tmp_path: Path) -> None:
    now: float = time.time()
    nanoseconds = lambda age_s: int((now - age_s - _APPLE_EPOCH) * 1e9)  # noqa: E731
    db: Path = tmp_path / "chat.db"
    _chat_db(
        db,
        [
            (nanoseconds(30), "+15550100", "Your Shop code is 991122", None, 0),
            (nanoseconds(20), "bank@alerts.com", None, b"streamtyped NSString\x01\x94\x84\x01+Code: 445566\x86", 0),
            (nanoseconds(10), "me", "I sent this one", None, 1),
            (nanoseconds(3600), "+15550100", "Old code 111111", None, 0),
        ],
    )
    newest: FoundMessage | None = read_messages(db, "", 10, now)
    assert newest is not None and newest.sender == "bank@alerts.com" and "445566" in newest.text
    assert 19.0 <= newest.age_s <= 21.0
    shop: FoundMessage | None = read_messages(db, "5550100", 10, now)
    assert shop is not None and shop.text == "Your Shop code is 991122"
    assert read_messages(db, "nobody", 10, now) is None
    # A window that starts after every message holds none.
    assert read_messages(db, "", 1, now + 3000) is None
    with pytest.raises(DesktopPermissionError, match="Full Disk Access"):
        read_messages(tmp_path / "missing" / "chat.db", "", 10, now)
